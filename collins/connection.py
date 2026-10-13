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
  The start is `--no-block`: `systemctl start` otherwise waits for the
  start job, which for a `Type=notify` unit is the whole startup (the
  login-shell capture, the store's first scan), and after a `stop` or a
  `service.restart` queues behind the deactivating unit's stop sequence
  (up to `STOP_BOUND_S`) — on a reconnect that is the window's main loop
  frozen with the banner unpainted. The wait that follows polls the
  socket anyway, so nothing is lost. What `--no-block` cannot report is a
  unit that starts and then dies (a stale `ExecStart` from an older
  `--install-desktop`, a service exiting 1): the start is "queued" and no
  socket ever appears. A **wait** that times out after a systemd start
  asks systemd (`unit_state`: `systemctl --user show -p ActiveState`, off
  the main loop): a unit still `activating` (a slow cold start) or
  `deactivating` (a graceful stop of up to `STOP_BOUND_S` that the start
  job is queued behind: `service.restart`, `systemctl --user stop` with
  sessions to close) is trusted and waited for another `WAIT_S`; one
  `inactive` or `failed` (or no answer) marks systemd failed
  (`systemd_failed`), tells the owner once (`on_notice`: the service will
  run unmanaged), and every later start spawns `collins-service` itself
  (*fallback*) until a systemd-started service connects again, which
  clears the mark. `started_how` is this round's word alone: cleared at
  the top of every attempt, so a round that skipped the start never reads
  an older one's.
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
    """`systemctl --user start --no-block` the unit: True when the start
    job was queued. Only for the default app id (the unit runs the
    default). `--no-block` because the caller may be the main loop (see
    the module docstring); the wait polls the socket."""
    if app_id != APP_ID or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "--no-block", UNIT],
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


def unit_state(app_id: str) -> str:
    """The unit's ``ActiveState`` (`systemctl --user show -p ActiveState
    --value`): ``active``, ``activating``, ``deactivating``, ``reloading``,
    ``inactive`` or ``failed``; ``""`` when systemctl is absent, errors or
    the id is not the default. Asked after a wait ran out on a systemd
    start, off the main loop."""
    if app_id != APP_ID or shutil.which("systemctl") is None:
        return ""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "-p", "ActiveState", "--value", UNIT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("systemctl --user show %s: %s", UNIT, exc)
        return ""
    if result.returncode != 0:
        return ""
    word = result.stdout.decode(errors="replace").strip().split("\n")[0]
    return word if word.isalpha() else ""


# What `unit_state` words mean after a wait ran out: still at it, or over.
UNIT_BUSY = frozenset({"active", "activating", "deactivating", "reloading"})
UNIT_OVER = frozenset({"inactive", "failed"})

# The owner's one word to the user when the fallback takes over (a msgid;
# the app translates it with `_()`).
FALLBACK_NOTICE = (
    "The Collins service is running unmanaged: its systemd unit failed. "
    "See journalctl --user -u collins-service.service"
)


def default_starter(app_id: str) -> str:
    """Start a service for *app_id* and say how: ``"systemd"``,
    ``"spawned"``. Raises OSError when neither way works."""
    if systemctl_start(app_id):
        return "systemd"
    return spawn_starter(app_id)


def spawn_starter(app_id: str) -> str:
    """Start a service for *app_id* by spawning it: the fallback once a
    systemd start was queued and no socket followed."""
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
        fallback: Callable[[str], str] | None = None,
        unit_state: Callable[[str], str] | None = None,
        schedule: Callable[[int, Callable[[], bool]], object] | None = None,
        run_async: Callable[[Callable[[], None]], None] | None = None,
        land: Callable[[Callable[[], None]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        on_state: Callable[[str], None] | None = None,
        on_connected: Callable[[bool], None] | None = None,
        on_lost: Callable[[str], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
    ) -> None:
        self.app_id = app_id
        self.link = link
        self._find = find or self._default_find
        self._start = start or default_starter
        self._fallback = fallback or spawn_starter
        self._unit_state = unit_state or globals()["unit_state"]
        self.on_notice = on_notice  # the owner's one-line word to the user (a toast)
        # Set when a systemd start was queued, the wait found no socket and
        # systemd says the unit is over (`inactive` / `failed`: a stale
        # ExecStart, an exit 1), which `--no-block` cannot report. Every
        # later start is the fallback, until a systemd-started service
        # connects again. A unit still `activating` or `deactivating` (a
        # slow cold start, a graceful stop ahead of the start job) is
        # trusted and waited for.
        self.systemd_failed = False
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

    def _start_service(self) -> None:
        """Start the service the way this process still trusts: systemd (or
        the injected starter) until a systemd start was found over with no
        socket behind it, the spawn fallback from then on."""
        starter = self._fallback if self.systemd_failed else self._start
        self.started_how = starter(self.app_id)

    def _keep_waiting(self) -> bool:
        """A wait ran out. True when the service is systemd's and systemd
        says the unit is still at it (`activating`: a slow cold start;
        `deactivating`: a graceful stop the start job is queued behind;
        `active` / `reloading`), so the wait goes on. False otherwise; a
        systemd start whose unit is over (`inactive`, `failed`, or no
        answer) marks systemd failed and the fallback takes the next start.
        Asks systemctl: callers keep it off the main loop."""
        if self.started_how != "systemd" or self.systemd_failed:
            return False
        state = self._unit_state(self.app_id)
        if state in UNIT_BUSY:
            log.info("connection: %s is %s; waiting on", UNIT, state)
            return True
        self._mark_systemd_failed(state)
        return False

    def _mark_systemd_failed(self, state: str) -> None:
        log.warning("connection: %s started but no socket appeared (unit %s); spawning from now on",
                    UNIT, state or "unknown")
        self.systemd_failed = True
        if self.on_notice is not None:
            try:
                self.on_notice(FALLBACK_NOTICE)
            except Exception:
                log.exception("connection: on_notice failed")

    def _systemd_regained(self) -> bool:
        """Asked at a successful handshake, off the main loop: True when the
        service answering is systemd's — one this round's own systemd start
        brought up, or, while the mark is set, one found live that this
        client did not start and whose unit systemd reports `active` (the
        user repaired and restarted the unit by hand)."""
        if self.started_how == "systemd":
            return True
        if self.systemd_failed and self.started_how == "":
            return self._unit_state(self.app_id) == "active"
        return False

    def _connected_how(self, regained: bool) -> None:
        """Connected: a service systemd started earns systemd its trust back."""
        if regained and self.systemd_failed:
            log.info("connection: %s answers again; systemd starts from now on", UNIT)
            self.systemd_failed = False

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
        `api.client.ConnectionLost` when no service can be reached, or
        OSError from the starter — the systemd-or-spawn one, or the spawn
        fallback taken once the unit was found over with no socket
        (`spawn_service` could not exec `collins-service`)."""
        self.started_how = ""
        self.path, found = self._find(self.app_id)
        if found != "live":
            self._set_state(STARTING)
            self._start_service()
            self._set_state(WAITING)
            while not self._wait_blocking(wait_s):
                if self._keep_waiting():
                    continue  # the unit is still starting or stopping: another wait
                if self.started_how != "systemd" or not self.systemd_failed:
                    self._give_up(wait_s)
                # The unit is over with no socket: spawn once, here, before
                # giving up (a stale unit must not block every launch).
                self._set_state(STARTING)
                self._start_service()
                self._set_state(WAITING)
                if not self._wait_blocking(wait_s):
                    self._give_up(wait_s)
                break
        self._connect()

    def _give_up(self, wait_s: float) -> None:
        from .api.client import ConnectionLost

        raise ConnectionLost(f"no service answered on {self.path} within {wait_s:.0f} s")

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
        self._connected_how(self._systemd_regained())
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
        self.started_how = ""  # this round's own word, or none when the start is skipped
        try:
            self.path, found = self._find(self.app_id)
            if found != "live":
                self._set_state(STARTING)
                self._start_service()
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
            if self.started_how == "systemd" and not self.systemd_failed:
                # Ask systemd off the main loop whether the unit is still at
                # it; the verdict lands here and either extends the wait or
                # marks the fallback and goes LOST.
                generation = self._generation

                def ask() -> None:
                    keep = self._keep_waiting()
                    self._land(lambda: self._timeout_verdict(keep, generation))

                self._run_async(ask)
                return False
            self._set_state(LOST)
            self._retry_later()
            return False
        self._pending = self._schedule(POLL_MS, lambda: self._poll(deadline))
        return False

    def _timeout_verdict(self, keep_waiting: bool, generation: int) -> None:
        if self.stopped or self.state != WAITING or generation != self._generation:
            return  # a loss or a newer attempt moved on meanwhile
        if keep_waiting:
            self._poll(self._now() + WAIT_S)
            return
        self._set_state(LOST)
        self._retry_later()

    def _try_connect(self) -> None:
        """The handshake on the owner's thread, the outcome on its loop."""
        self._set_state(CONNECTING)
        self._generation += 1
        generation = self._generation

        def handshake() -> None:
            try:
                self.link.connect()
                self.link.prove_local()
                regained = self._systemd_regained()  # may ask systemctl: here, off the loop
            except Exception as exc:
                error = exc  # the except name is cleared before the landing runs
                self._land(lambda: self._connect_failed(error, generation))
                return
            self._land(lambda: self._connect_landed(generation, regained))

        self._run_async(handshake)

    def _connect_landed(self, generation: int | None = None, regained: bool = False) -> None:
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
        self._connected_how(regained)
        first = not self.connected_once
        self.connected_once = True
        self.attempts = 0
        self._set_state(CONNECTED)
        if self.on_connected is not None:
            self.on_connected(first)

    def _connect_failed(self, exc: Exception, generation: int | None = None) -> None:
        if self.stopped or (generation is not None and generation != self._generation):
            return
        log.warning("connection: connect failed: %s", exc)
        self._set_state(LOST)
        self._retry_later()

    def stop(self) -> None:
        """The client is quitting: no more retries."""
        self.stopped = True
        self.link.on_lost = None
        self.link.close()
