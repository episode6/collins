# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`ConnectionManager`: how the client finds, starts and keeps its service.

Split-service spec §3.20, D5. A state machine, GTK-free, with every side
effect injectable so `tests/test_connection.py` drives it with fakes:

    find ──► start ──► wait ──► connect ──► connected
     ▲                                          │
     └──────────── backoff ◄──── lost ◄─────────┘

- **find**: the socket path for this app id (`api.server.socket_path`)
  and whether a service answers on it (`probe_socket`).
- **start**: when nothing answers, `systemctl --user start
  collins-service.service` where `systemctl` exists and the app id is the
  default (the unit the packages ship and `collins --install-desktop`
  installs; `Restart=on-failure` is systemd's), else spawn
  `collins-service --app-id <id>` detached (`start_new_session=True`), the
  fallback with no systemd (the debug instance, a check, the macOS port).
  A `systemctl` that fails (no unit installed) falls back to the spawn.
- **wait**: poll the socket every `POLL_MS` for up to `WAIT_S`.
- **connect**: both channels and the hello (`SocketLink.connect`), the
  local proof from the client's own filesystem (`prove_local`), then the
  owner's `on_connected(first)` (the app binds the mirrors on the first,
  resets and refills them on a reconnect, and subscribes in both).
- **lost** (the link's `on_lost`): the owner's `on_lost` (the banner), then
  backoff 1, 2, 5, 10, 30 s and every 30 s after, each attempt a fresh
  find + start + wait + connect: a service that died is restarted by the
  client that notices (the unit's `Restart=on-failure` first).

`start_local()` runs the first connect synchronously at startup (no window
exists yet to show a banner); the reconnects run on the main loop through
the injected `schedule` (`GLib.timeout_add` in the app), never blocking
longer than one connect's handshake.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from . import APP_ID
from .api import server as api_server

log = logging.getLogger(__name__)

UNIT = "collins-service.service"
POLL_MS = 100
WAIT_S = 10.0
BACKOFF_S = (1, 2, 5, 10, 30)

IDLE = "idle"
STARTING = "starting"
WAITING = "waiting"
CONNECTING = "connecting"
CONNECTED = "connected"
LOST = "lost"
# The service answered and speaks no protocol this client does (§3.21):
# no retry, the owner asks the person.
MISMATCH = "mismatch"

# How long a reconnect after a restart this client asked for waits for the
# old service to finish ending its sessions before it starts another.
RESTART_WAIT_S = 20.0


def _is_mismatch(exc: BaseException) -> bool:
    from .api.client import ProtocolMismatch

    return isinstance(exc, ProtocolMismatch)


def pid_is_alive(pid: int) -> bool:
    """Whether *pid* names a process that has not exited (a zombie counts
    as gone)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


def service_pid(path: str) -> int | None:
    """The pid of the process listening on the service socket at *path*
    (the peer's credentials, SO_PEERCRED: the listener's at its listen),
    or None when nothing answers or it is another user's. What the
    mismatch dialog's *Restart service* stops, since a service that
    refused our hello serves no `service.restart`."""
    import socket
    import struct

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(path)
            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    pid, uid, _gid = struct.unpack("3i", raw)
    if pid <= 0 or uid != os.getuid():
        return None
    return pid


def stop_service(path: str) -> int | None:
    """SIGTERM the service listening at *path* (its stop sequence ends the
    sessions, recorded to resume, and exits); returns its pid, None when
    there was nothing to stop."""
    import signal

    pid = service_pid(path)
    if pid is None:
        return None
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        log.warning("connection: could not stop the service (pid %d): %s", pid, exc)
        return None
    return pid


def service_argv(app_id: str) -> list[str]:
    """How to run the service for *app_id*: the installed `collins-service`
    when there is one, else this interpreter on the module (a source
    checkout, `start-debug`)."""
    found = shutil.which("collins-service")
    if found:
        return [found, "--app-id", app_id]
    return [sys.executable, "-m", "collins.service.main", "--app-id", app_id]


def spawn_service(app_id: str, env: dict[str, str] | None = None) -> int:
    """Spawn the service detached; returns its pid. Raises OSError.

    Its stdio is its own: a detached service that inherited the window's
    stdout and stderr would hold them open after the window is gone (a
    harness reading the window's output never sees its end), so both go
    to the null device, unless ``COLLINS_LOG`` is set, when whoever set it
    wants the service's lines beside the window's."""
    env = dict(os.environ if env is None else env)
    output = None if env.get("COLLINS_LOG") else subprocess.DEVNULL
    proc = subprocess.Popen(
        service_argv(app_id),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=output,
        env=env,
        close_fds=True,
    )
    return proc.pid


def systemctl_start(app_id: str) -> bool:
    """`systemctl --user start` the unit: True when it reported success.
    Only for the default app id (the unit runs the default)."""
    if app_id != APP_ID or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", UNIT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("systemctl --user start %s: %s", UNIT, exc)
        return False
    if result.returncode != 0:
        log.info("systemctl --user start %s failed: %s", UNIT, result.stderr.decode(errors="replace").strip())
        return False
    return True


def default_starter(app_id: str) -> str:
    """Start a service for *app_id* and say how: ``"systemd"``,
    ``"spawned"``. Raises OSError when neither way works."""
    if systemctl_start(app_id):
        return "systemd"
    spawn_service(app_id)
    return "spawned"


class ConnectionManager:
    """See the module docstring.

    *link* has `connect()`, `prove_local()`, `close()` and takes `on_lost`
    (a `SocketLink`); *find* answers ``(path, state)`` for the app id
    (`probe_socket`'s word); *start* starts a service; *schedule(ms, fn)*
    runs *fn* later on the owner's loop (*fn* returns False to stop);
    *sleep* and *now* are the clock. The owner hears `on_state(state)`,
    `on_connected(first)` and `on_lost(reason)`."""

    def __init__(
        self,
        app_id: str,
        link,
        *,
        find: Callable[[str], tuple[str, str]] | None = None,
        start: Callable[[str], str] | None = None,
        schedule: Callable[[int, Callable[[], bool]], object] | None = None,
        run_async: Callable[[Callable[[], None]], None] | None = None,
        land: Callable[[Callable[[], None]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        on_state: Callable[[str], None] | None = None,
        on_connected: Callable[[bool], None] | None = None,
        on_lost: Callable[[str], None] | None = None,
        on_mismatch: Callable[[Exception], None] | None = None,
        cancel: Callable[[object], None] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self.app_id = app_id
        self.link = link
        self._find = find or self._default_find
        self._start = start or default_starter
        self._schedule = schedule
        # A reconnect's handshake (up to CONNECT_TIMEOUT_S of waiting) runs
        # on a thread the owner provides and lands on its loop, so the
        # window keeps drawing its banner meanwhile; the first connect at
        # startup is synchronous, no window exists yet.
        self._run_async = run_async or self._thread
        self._land = land or (lambda fn: fn())
        self._sleep = sleep
        self._now = now
        self.on_state = on_state
        self.on_connected = on_connected
        self.on_lost = on_lost
        self.on_mismatch = on_mismatch
        self._cancel = cancel
        self._pid_alive = pid_alive or pid_is_alive
        # The service a restart this client asked for is waiting on (its
        # pid), and how long the reconnect has waited for it to go.
        self._restart_pid: int | None = None
        self._restart_waited = 0
        self.state = IDLE
        self.path = ""
        self.started_how = ""  # "", "systemd" or "spawned": how this client started it
        self.attempts = 0  # reconnect attempts since the last loss
        self.connected_once = False
        self.stopped = False
        # Each handshake is one attempt: a landing from an attempt the link
        # has lost since (or a newer attempt superseded) is ignored.
        self._generation = 0
        self._pending = None  # the scheduled retry, if the schedule hands one back
        link.on_lost = self._on_link_lost

    @staticmethod
    def _thread(fn: Callable[[], None]) -> None:
        import threading

        threading.Thread(target=fn, name="collins-reconnect", daemon=True).start()

    @staticmethod
    def _default_find(app_id: str) -> tuple[str, str]:
        path = api_server.socket_path(app_id)
        return path, api_server.probe_socket(path)

    def _set_state(self, state: str) -> None:
        self.state = state
        if self.on_state is not None:
            try:
                self.on_state(state)
            except Exception:
                log.exception("connection: on_state failed")

    # -- the first connect, blocking ---------------------------------------------------

    def start_local(self, wait_s: float = WAIT_S) -> None:
        """find → start → wait → connect, synchronously. Raises
        `api.client.ConnectionLost` (or OSError from the starter) when no
        service can be reached."""
        self.path, found = self._find(self.app_id)
        if found != "live":
            self._set_state(STARTING)
            self.started_how = self._start(self.app_id)
            self._set_state(WAITING)
            if not self._wait_blocking(wait_s):
                from .api.client import ConnectionLost

                raise ConnectionLost(f"no service answered on {self.path} within {wait_s:.0f} s")
        self._connect()

    def _wait_blocking(self, wait_s: float) -> bool:
        deadline = self._now() + wait_s
        while self._now() < deadline:
            self.path, found = self._find(self.app_id)
            if found == "live":
                return True
            self._sleep(POLL_MS / 1000)
        return False

    def _connect(self) -> None:
        self._set_state(CONNECTING)
        self.link.connect()
        self.link.prove_local()
        first = not self.connected_once
        self.connected_once = True
        self.attempts = 0
        self._set_state(CONNECTED)
        if self.on_connected is not None:
            self.on_connected(first)

    # -- loss and reconnect, on the owner's loop ----------------------------------------

    def lost(self, reason: str) -> None:
        """The owner found the link dead (a resubscribe refused ``gone``):
        the same road as the link's own `on_lost`."""
        self._on_link_lost(reason)

    def _on_link_lost(self, reason: str) -> None:
        if self.stopped or self.state == LOST:
            return
        self._generation += 1  # a handshake in flight is nobody's now
        self._set_state(LOST)
        if self.on_lost is not None:
            try:
                self.on_lost(reason)
            except Exception:
                log.exception("connection: on_lost failed")
        self._retry_later()

    def backoff_s(self) -> float:
        index = min(self.attempts, len(BACKOFF_S) - 1)
        return float(BACKOFF_S[index])

    def _retry_later(self) -> None:
        if self.stopped or self._schedule is None:
            return
        delay = self.backoff_s()
        self.attempts += 1
        self._pending = self._schedule(int(delay * 1000), self._retry)

    def _retry(self) -> bool:
        """One reconnect attempt: find + start, then a polled wait that
        yields to the loop between polls."""
        self._pending = None
        if self.stopped or self.state == CONNECTED:
            return False
        if self._old_service_alive():
            # The service this client restarted is still on its way out
            # (ending its sessions): look again in a beat, without
            # counting an attempt, for a bounded while.
            if self._restart_waited < RESTART_WAIT_S * 1000:
                self._restart_waited += POLL_MS * 5
                self._pending = self._schedule(POLL_MS * 5, self._retry)
                return False
            self._restart_pid = None
        self._restart_waited = 0
        try:
            self.path, found = self._find(self.app_id)
            if found != "live":
                self._set_state(STARTING)
                self.started_how = self._start(self.app_id)
        except Exception as exc:
            log.warning("connection: could not start the service: %s", exc)
            self._set_state(LOST)
            self._retry_later()
            return False
        self._set_state(WAITING)
        deadline = self._now() + WAIT_S
        self._poll(deadline)
        return False

    def _poll(self, deadline: float) -> bool:
        if self.stopped:
            return False
        self.path, found = self._find(self.app_id)
        if found == "live":
            self._try_connect()
            return False
        if self._now() >= deadline:
            self._set_state(LOST)
            self._retry_later()
            return False
        self._pending = self._schedule(POLL_MS, lambda: self._poll(deadline))
        return False

    def _try_connect(self) -> None:
        """The handshake on the owner's thread, the outcome on its loop."""
        self._set_state(CONNECTING)
        self._generation += 1
        generation = self._generation

        def handshake() -> None:
            try:
                self.link.connect()
                self.link.prove_local()
            except Exception as exc:
                error = exc  # the except name is cleared before the landing runs
                self._land(lambda: self._connect_failed(error, generation))
                return
            self._land(lambda: self._connect_landed(generation))

        self._run_async(handshake)

    def _connect_landed(self, generation: int | None = None) -> None:
        if self.stopped:
            return
        if generation is not None and generation != self._generation:
            return  # a newer attempt is under way
        if not getattr(self.link, "connected", True):
            # Lost between the handshake and this landing: the loss's own
            # retry is scheduled (or will be); nothing to celebrate.
            if self.state != LOST:
                self._set_state(LOST)
                self._retry_later()
            return
        first = not self.connected_once
        self.connected_once = True
        self.attempts = 0
        self._set_state(CONNECTED)
        if self.on_connected is not None:
            self.on_connected(first)

    def _connect_failed(self, exc: Exception, generation: int | None = None) -> None:
        if self.stopped or (generation is not None and generation != self._generation):
            return
        if _is_mismatch(exc):
            # No retry speaks a protocol the service does not: the owner
            # asks the person (the mismatch dialog, §3.21).
            log.warning("connection: %s", exc)
            self._set_state(MISMATCH)
            if self.on_mismatch is not None:
                try:
                    self.on_mismatch(exc)
                except Exception:
                    log.exception("connection: on_mismatch failed")
            return
        log.warning("connection: connect failed: %s", exc)
        self._set_state(LOST)
        self._retry_later()

    # -- a restart this client asked for (PR-1.12c) ------------------------------------

    def expect_restart(self, pid: int | None) -> None:
        """The service (*pid*, from `service.status`) was asked to restart:
        the next reconnect waits for that process to be gone before it
        starts another, so the two never share the state files."""
        self._restart_pid = pid if isinstance(pid, int) and pid > 0 else None

    def _old_service_alive(self) -> bool:
        pid = self._restart_pid
        if pid is None:
            return False
        if not self._pid_alive(pid):
            self._restart_pid = None
            return False
        return True

    def reconnect_now(self) -> None:
        """Try again at once (the mismatch dialog's *Restart service*, once
        the old service is gone): the lost path, with no backoff."""
        if self.stopped:
            return
        if self._pending is not None and self._cancel is not None:
            try:
                self._cancel(self._pending)
            except Exception:
                pass
        self._pending = None
        self.attempts = 0
        self._set_state(LOST)
        self._retry()

    def stop(self) -> None:
        """The client is quitting: no more retries."""
        self.stopped = True
        self.link.on_lost = None
        self.link.close()
